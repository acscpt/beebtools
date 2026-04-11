# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("beebtools")
except PackageNotFoundError:
    # Package is not installed (e.g. running directly from source tree)
    __version__ = "unknown"

"""
beebtools - BBC Micro DFS and ADFS disc image tool.

Supports DFS (.ssd, .dsd) and ADFS (.adf, .adl) disc image formats with
full read and write support. BBC BASIC programs are detokenized to produce
LIST-style plain text output, with an optional pretty-printer that adds
operator spacing and handles copy-protection anti-listing traps.

Usage as a library:
    from beebtools import openImage, detokenize, prettyPrint

    with openImage("mydisc.adf") as image:
        for side in image:
            for entry in side.readCatalogue():
                data = side.readFile(entry)
                lines = detokenize(data)
                print("\\n".join(prettyPrint(lines)))

Usage as a CLI tool:
    beebtools cat     <image>
    beebtools search  <image> <pattern> [-i] [--pretty]
    beebtools extract <image> <filename> [-o FILE] [--pretty]
    beebtools extract <image> -a [-d DIR] [--pretty] [--inf]
    beebtools create  <output> [--title TITLE] [--boot OPTION]
    beebtools add     <image> <file> [--name N] [--load L] [--exec E]
    beebtools delete  <image> <filename>
    beebtools build   <dir> <output> [--title TITLE] [--boot OPTION]

Modules:
    tokens        -- BBC BASIC II token table and constants
    basic         -- BASIC facade: tokenize, detokenize, classify, escape
    pretty        -- operator spacing and anti-listing trap handling
    dfs           -- DFS disc image reader and writer (.ssd and .dsd)
    adfs          -- ADFS disc image reader and writer (.adf and .adl)
    inf           -- .inf sidecar file parser and formatter
    disc          -- high-level disc operations (extract, search, build)
    cli           -- command-line interface
"""

from .basic import (
    basicProgramSize, compactLine, detokenize, decodeLineRef,
    tokenize, encodeLineRef,
    prettyPrint,
    looksLikeTokenizedBasic, looksLikePlainText, classifyFileType,
    escapeNonAscii, unescapeNonAscii, hasEscapes,
)
from .boot import BootOption
from .entry import DiscEntry, DiscCatalogue, DiscFile, DiscError, DiscFormatError, isBasicExecAddr
from .codec import registerCodec

# Register the "bbc" codec so bytes.decode("bbc") / str.encode("bbc") work
# as soon as the package is imported.
registerCodec()
from .dfs import (
    DFSEntry,
    DFSCatalogue,
    DFSImage,
    DFSSide,
    DFSError,
    DFSFormatError,
    openDiscImage,
    createDiscImage,
    validateDfsName,
    splitDfsPath,
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
    createAdfsImage,
    validateAdfsName,
    ADFS_S_SECTORS,
    ADFS_M_SECTORS,
    ADFS_L_SECTORS,
)
from .image import openImage, createImage, DiscSide, DiscImage
from .inf import InfData, parseInf, formatInf
from .disc import (
    search, extractAll, buildImage, createImageFile,
    sortCatalogueEntries,
    readCatalogue, CatalogueListing, CatalogueEntry,
    extractFile, ExtractedFile, addFile, addFileTo, qualifyDiscPath,
    writeBasicText, readBasicText, formatEntryInf,
    getTitle, setTitle, getBoot, setBoot, discInfo, DiscInfo,
    getFileAttribs, setFileAttribs, FileAttribs,
    deleteFile, renameFile, compactDisc, makeDirectory,
)
from .cli import main

__all__ = [
    "detokenize",
    "decodeLineRef",
    "tokenize",
    "encodeLineRef",
    "compactLine",
    "prettyPrint",
    # New DFS types
    "DFSEntry",
    "DFSCatalogue",
    "DFSImage",
    "DFSSide",
    "DFSError",
    "DFSFormatError",
    "BootOption",
    # Shared contracts (entry.py)
    "DiscEntry",
    "DiscCatalogue",
    "DiscFile",
    "DiscError",
    "DiscFormatError",
    "isBasicExecAddr",
    "registerCodec",
    "openDiscImage",
    "createDiscImage",
    "validateDfsName",
    "splitDfsPath",
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
    "createAdfsImage",
    "validateAdfsName",
    "ADFS_S_SECTORS",
    "ADFS_M_SECTORS",
    "ADFS_L_SECTORS",
    # Image dispatcher
    "openImage",
    "createImage",
    "DiscSide",
    "DiscImage",
    # Orchestration (disc.py)
    "search",
    "extractAll",
    "buildImage",
    "createImageFile",
    "sortCatalogueEntries",
    "readCatalogue",
    "CatalogueListing",
    "CatalogueEntry",
    "extractFile",
    "ExtractedFile",
    "addFile",
    "addFileTo",
    "qualifyDiscPath",
    "writeBasicText",
    "readBasicText",
    "formatEntryInf",
    # Disc mutation (disc.py)
    "getTitle",
    "setTitle",
    "getBoot",
    "setBoot",
    "discInfo",
    "DiscInfo",
    "getFileAttribs",
    "setFileAttribs",
    "FileAttribs",
    "deleteFile",
    "renameFile",
    "compactDisc",
    "makeDirectory",
    # BASIC facade (basic.py)
    "looksLikeTokenizedBasic",
    "looksLikePlainText",
    "classifyFileType",
    "escapeNonAscii",
    "unescapeNonAscii",
    "hasEscapes",
    "main",
]

