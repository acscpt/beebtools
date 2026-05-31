# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Disc image format dispatch.

Routes an input path or an output path to the appropriate format
engine by inspecting its file extension, then delegates to the
format-specific opener or creator. This keeps the individual
format engines independent of each other.

DiscSide and DiscImage are re-exported here for callers that
historically imported them from this module.

Supported formats:
    .ssd  -- DFS single-sided
    .dsd  -- DFS double-sided interleaved
    .adf  -- ADFS single-sided (old map, small directory)
    .adl  -- ADFS double-sided (old map, small directory)
"""

from .adfs import (
    ADFS_L_SECTORS, ADFS_M_SECTORS, ADFS_S_SECTORS,
    createAdfsImage, openAdfsImage,
)
from .boot import BootOption
from .dfs import (
    DFSFormatError, createDiscImage, openDiscImage,
    splitDsd, mergeDsd,
)
from .entry import DiscImage, DiscSide, DiscError


__all__ = [
    "DiscImage",
    "DiscSide",
    "openImage",
    "createImage",
    "splitImage",
    "mergeImages",
]


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
        A disc image of the detected format.

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
        A blank disc image of the format implied by the extension.

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


# ---------------------------------------------------------------------------
# split / merge dispatch
# ---------------------------------------------------------------------------
#
# Routing rules differ slightly from openImage / createImage: extension
# is used only as a positive *block* for ADFS, not as the sole signal
# for DFS. Real-world disc images frequently carry odd extensions
# (.img, .disc, no extension at all), so anything that is not
# unambiguously ADFS is passed through to the DFS layer where the
# byte length is validated against the legal SSD / DSD capacities.
# That gives genuine content-based dispatch without needing a full
# format-sniffing layer, because DFS already raises a clear DiscError
# on any file whose size does not match a real DFS capacity.
#
# ADFS is rejected with a tailored message because an .adl is a single
# filesystem spanning two surfaces - it cannot be split into two
# independent discs, so the operation is undefined rather than merely
# unimplemented.


def _rejectAdfsForSplitMerge(path: str, role: str) -> None:
    """Block ADFS extensions from split / merge with a clear message.

    Anything else - known DFS extensions, unknown extensions, no
    extension at all - is allowed through; size validation in the
    DFS layer will reject genuinely invalid files.
    """

    ext = _extractExtension(path)

    if ext in _ADFS_EXTENSIONS:
        raise DiscError(
            f"split and merge are not supported on ADFS images "
            f"({role} {path!r}); ADFS .adl images are a single "
            f"filesystem spanning two surfaces, not two separable discs"
        )


def splitImage(source, *output_names, sequential=False, force=False):
    """Split a double-sided disc image into two single-sided halves.

    Dispatches by content: any input that is not an ADFS image is
    handed to the DFS engine, which validates the byte length and
    rejects anything that is not a legal DSD capacity. This means
    files with unconventional extensions (.img, .disc, none at all)
    are accepted as long as their contents are a real DSD.
    """

    _rejectAdfsForSplitMerge(source, "source")

    return splitDsd(
        source, *output_names,
        sequential=sequential, force=force,
    )


def mergeImages(side0_path, side1_path, output, sequential=False, force=False):
    """Combine two single-sided disc images into one double-sided image.

    Dispatches by content for the two inputs and by extension for the
    output (which does not yet exist on disc). ADFS extensions on any
    path are rejected; anything else flows through to DFS where size
    validation enforces real SSD capacities.
    """

    # Validate every path before touching the filesystem so a wrong
    # extension is reported immediately and consistently. The output
    # path is checked here even though it does not exist yet, because
    # writing DFS bytes to a .adl path would be silently misleading.
    _rejectAdfsForSplitMerge(side0_path, "side-0 image")
    _rejectAdfsForSplitMerge(side1_path, "side-1 image")
    _rejectAdfsForSplitMerge(output, "output image")

    return mergeDsd(
        side0_path, side1_path, output,
        sequential=sequential, force=force,
    )
