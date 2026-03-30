# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("beebtools")
except PackageNotFoundError:
    # Package is not installed (e.g. running directly from source tree)
    __version__ = "unknown"

"""
beebtools - BBC Micro DFS disc image tool.

Supports .ssd (single-sided) and .dsd (double-sided interleaved) formats.
BBC BASIC programs are detokenized to produce LIST-style plain text output,
with an optional pretty-printer that adds operator spacing and handles
copy-protection anti-listing traps.

Usage as a library:
    from beebtools import openDiscImage, detokenize, prettyPrint

    sides = openDiscImage("mydisc.dsd")
    for disc in sides:
        title, entries = disc.readCatalogue()
        for entry in entries:
            data = disc.readFile(entry)
            lines = detokenize(data)
            print("\\n".join(prettyPrint(lines)))

Usage as a CLI tool:
    beebtools cat  <image>
    beebtools extract <image> <filename> [-o FILE]
    beebtools extract <image> -a [-d DIR] [--pretty]

Modules:
    tokens        -- BBC BASIC II token table and constants
    detokenize    -- tokenized binary to LIST-style text
    pretty        -- operator spacing and anti-listing trap handling
    dfs           -- DFS disc image reader (.ssd and .dsd)
    cli           -- command-line interface
"""

from .detokenize import detokenize, decodeLineRef
from .pretty import prettyPrint
from .dfs import isBasic, looksLikeText, looksLikePlainText, openDiscImage, DFSDisc, sortCatalogueEntries
from .disc import search, extractAll
from .cli import main

__all__ = [
    "detokenize",
    "decodeLineRef",
    "prettyPrint",
    "isBasic",
    "looksLikeText",
    "looksLikePlainText",
    "openDiscImage",
    "DFSDisc",
    "sortCatalogueEntries",
    "search",
    "extractAll",
    "main",
]

