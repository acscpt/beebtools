# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Output helpers shared across command modules.

ANSI colour wrappers, the disc format label, and the resource bundle
loader used by command modules that need format-specific help text.
"""

import importlib
import os
import pkgutil
import sys
from typing import Dict

import beebtools

from ..entry import FileType


# ---------------------------------------------------------------------------
# ANSI colour codes
# ---------------------------------------------------------------------------

BOLD    = "\x1b[1m"
CYAN    = "\x1b[96m"
GREEN   = "\x1b[92m"
YELLOW  = "\x1b[93m"
MAGENTA = "\x1b[95m"
RED     = "\x1b[91m"
GREY    = "\x1b[90m"
RESET   = "\x1b[0m"


def colour(text: str, code: str, enabled: bool) -> str:
    """Wrap text in an ANSI escape sequence when colour is enabled."""
    if not enabled:
        return text
    return f"{code}{text}{RESET}"


def useColour() -> bool:
    """Return True when stdout is attached to a terminal."""
    return sys.stdout.isatty()


# ---------------------------------------------------------------------------
# Colour mapping for classified file types in catalogue listings.
# ---------------------------------------------------------------------------

TAG_COLOURS = {
    FileType.BASIC:     (CYAN,    FileType.BASIC.value),
    FileType.BASIC_MC:  (MAGENTA, FileType.BASIC_MC.value),
    FileType.BASIC_ISH: (GREEN,   FileType.BASIC_ISH.value),
    FileType.TEXT:      (YELLOW,  FileType.TEXT.value),
}


# ---------------------------------------------------------------------------
# Disc format label
# ---------------------------------------------------------------------------

def formatLabel(output_path: str, tracks: int, size_bytes: int) -> str:
    """Return a human-readable disc format label for CLI output.

    Derives the label from the file extension of output_path. DFS
    formats report the track count; ADFS formats report the image
    size in kilobytes.
    """
    ext = os.path.splitext(output_path)[1].lower()
    labels = {
        ".ssd": f"{tracks}-track SSD",
        ".dsd": f"{tracks}-track DSD",
        ".adf": f"{size_bytes // 1024}K ADF",
        ".adl": f"{size_bytes // 1024}K ADL",
    }
    return labels.get(ext, ext)


# ---------------------------------------------------------------------------
# Resource bundle loader (used by attrib for format-specific help text)
# ---------------------------------------------------------------------------

def loadResourceBundles(consumer: str = "cli") -> Dict[str, str]:
    """Merge ``RESOURCES[consumer]`` from every ``*_resources`` module.

    Walks ``beebtools.__path__`` with ``pkgutil.iter_modules``, imports
    each module whose name ends in ``_resources``, and collects entries
    under the requested consumer key into a single flat dict. When
    multiple bundles contribute the same key, their values are
    concatenated so every format's help block reaches the user.
    """

    merged: Dict[str, str] = {}

    # Sort by module name so output ordering is deterministic across
    # runs and platforms instead of depending on filesystem order.
    module_names = sorted(
        info.name for info in pkgutil.iter_modules(beebtools.__path__)
        if info.name.endswith("_resources")
    )

    for module_name in module_names:

        module = importlib.import_module(f"beebtools.{module_name}")

        # Resource modules expose a RESOURCES dict keyed by consumer
        # name. Missing or malformed modules are ignored rather than
        # exploding at startup.
        resources = getattr(module, "RESOURCES", None)
        if not isinstance(resources, dict):
            continue

        bundle = resources.get(consumer)
        if not isinstance(bundle, dict):
            continue

        # Concatenate on key clashes so each format's block contributes
        # to the combined text. A blank line separates existing content
        # from the newcomer for legibility.
        for key, value in bundle.items():
            if key in merged:
                merged[key] = merged[key].rstrip("\n") + "\n\n" + value
            else:
                merged[key] = value

    return merged
