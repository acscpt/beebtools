# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Unified disc image opener with format auto-detection.

Provides openImage() which detects the filing system format from the
file extension and delegates to the appropriate format-specific opener.
This keeps the DFS and ADFS modules independent of each other.

Supported formats:
    .ssd  -- DFS single-sided
    .dsd  -- DFS double-sided interleaved
    .adf  -- ADFS single-sided (old map, small directory)
    .adl  -- ADFS double-sided (old map, small directory)
"""

from typing import Union

from .adfs import ADFSImage, ADFSFormatError, openAdfsImage
from .dfs import DFSImage, DFSFormatError, openDiscImage


# Extension-to-format mapping. Extensions are matched case-insensitively.
_DFS_EXTENSIONS = {".ssd", ".dsd"}
_ADFS_EXTENSIONS = {".adf", ".adl"}


def openImage(path: str) -> Union[DFSImage, ADFSImage]:
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
