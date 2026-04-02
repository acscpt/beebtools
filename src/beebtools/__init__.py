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

    image = openDiscImage("mydisc.dsd")
    for side in image.sides:
        catalogue = side.readCatalogue()
        for entry in catalogue.entries:
            data = side.readFile(entry)
            lines = detokenize(data)
            print("\\n".join(prettyPrint(lines)))

Usage as a CLI tool:
    beebtools cat     <image>
    beebtools search  <image> <pattern> [-i] [--pretty]
    beebtools extract <image> <filename> [-o FILE] [--pretty]
    beebtools extract <image> -a [-d DIR] [--pretty] [--inf]
    beebtools create  <image> [--title TITLE] [--boot OPTION]
    beebtools add     <image> <file> [--name N] [--load L] [--exec E]
    beebtools delete  <image> <filename>
    beebtools build   <dir> <image> [--title TITLE] [--boot OPTION]

Modules:
    tokens        -- BBC BASIC II token table and constants
    detokenize    -- tokenized binary to LIST-style text
    tokenize      -- LIST-style text to tokenized binary
    pretty        -- operator spacing and anti-listing trap handling
    dfs           -- DFS disc image reader and writer (.ssd and .dsd)
    inf           -- .inf sidecar file parser and formatter
    disc          -- high-level disc operations (extract, search, build)
    cli           -- command-line interface
"""

from .detokenize import detokenize, decodeLineRef
from .tokenize import tokenize, encodeLineRef
from .pretty import prettyPrint
from .dfs import (
    DFSEntry,
    DFSCatalogue,
    DFSImage,
    DFSSide,
    DFSError,
    DFSFormatError,
    BootOption,
    openDiscImage,
    createDiscImage,
    looksLikeTokenizedBasic,
    looksLikePlainText,
    sortCatalogueEntries,
    validateDfsName,
    # Backward-compatibility aliases
    isBasic,
    looksLikeText,
    DFSDisc,
)
from .adfs import (
    ADFSEntry,
    ADFSCatalogue,
    ADFSDirectory,
    ADFSFreeSpaceMap,
    ADFSImage,
    ADFSSide,
    ADFSError,
    ADFSFormatError,
    openAdfsImage,
)
from .image import openImage
from .inf import InfData, parseInf, formatInf
from .disc import search, extractAll, buildImage
from .cli import main

__all__ = [
    "detokenize",
    "decodeLineRef",
    "tokenize",
    "encodeLineRef",
    "prettyPrint",
    # New DFS types
    "DFSEntry",
    "DFSCatalogue",
    "DFSImage",
    "DFSSide",
    "DFSError",
    "DFSFormatError",
    "BootOption",
    "openDiscImage",
    "createDiscImage",
    "looksLikeTokenizedBasic",
    "looksLikePlainText",
    "sortCatalogueEntries",
    "validateDfsName",
    # Backward-compatibility aliases
    "isBasic",
    "looksLikeText",
    "DFSDisc",
    # .inf sidecar format
    "InfData",
    "parseInf",
    "formatInf",
    # ADFS types
    "ADFSEntry",
    "ADFSCatalogue",
    "ADFSDirectory",
    "ADFSFreeSpaceMap",
    "ADFSImage",
    "ADFSSide",
    "ADFSError",
    "ADFSFormatError",
    "openAdfsImage",
    # Image dispatcher
    "openImage",
    # Orchestration
    "search",
    "extractAll",
    "buildImage",
    "main",
]

