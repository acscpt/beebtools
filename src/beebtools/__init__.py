# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""beebtools - BBC Micro DFS and ADFS disc image toolkit.

Supports DFS (.ssd, .dsd) and ADFS (.adf, .adl) disc image formats
with full read and write support. BBC BASIC programs are detokenized
to produce LIST-style plain text output, with an optional
pretty-printer that adds operator spacing and handles copy-protection
anti-listing traps.


Quick start (library)
---------------------

    from beebtools import extractAll, search, buildImage, readCatalogue

    # List the catalogue of a disc image
    listings = readCatalogue("mydisc.ssd", inspect=True)
    for listing in listings:
        for ce in listing.entries:
            print(f"{ce.entry.fullName}  {ce.file_type}")

    # Extract every file, writing .inf sidecars and pretty-printed BASIC
    extractAll("elite.ssd", "output/", pretty=True, inf=True)

    # Search all BASIC programs on a disc for a substring
    matches = search("games.ssd", "GOTO", ignore_case=True)

    # Build a disc image from a directory of files and .inf sidecars
    image_bytes = buildImage("src/", "output.ssd", title="MY DISC")


Public API tiers
----------------

Tier 1, high-level operations (start here):

    openImage, createImage, createImageFile
    readCatalogue, extractFile, extractAll, search, buildImage
    addFile, deleteFile, renameFile, compactDisc, makeDirectory
    getTitle, setTitle, getBoot, setBoot, discInfo
    getFileAttribs, setFileAttribs

Tier 2, types (return values and type hints):

    DiscImage, DiscSide, DiscEntry, DiscCatalogue, DiscFile
    ExtractedFile, CatalogueListing, CatalogueEntry
    DiscInfo, FileAttribs, BootOption, FileType
    DiscError, DiscFormatError

Tier 3, BASIC transforms:

    tokenize, detokenize, prettyPrint
    classifyFileType, looksLikeTokenizedBasic, looksLikePlainText
    escapeNonAscii, unescapeNonAscii, hasEscapes
    compactLine, encodeLineRef, decodeLineRef

Tier 4, format-specific:

    DFSEntry, DFSCatalogue, DFSSide, DFSImage
    DFSError, DFSFormatError
    openDiscImage, createDiscImage, validateDfsName, splitDfsPath

    ADFSEntry, ADFSCatalogue, ADFSDirectory, ADFSFreeSpaceMap
    ADFSSide, ADFSImage
    ADFSError, ADFSFormatError
    openAdfsImage, createAdfsImage, validateAdfsName
    ADFS_S_SECTORS, ADFS_M_SECTORS, ADFS_L_SECTORS, ADFS_ROOT_SECTOR

    parseInf, formatInf, formatEntryInf, InfData
    addFileTo, qualifyDiscPath, sortCatalogueEntries
    writeBasicText, readBasicText
    isBasicExecAddr, registerCodec


CLI usage
---------

    beebtools cat | search | extract | create | add | delete |
              rename | build | title | boot | disc | attrib |
              compact | mkdir

Run `beebtools <command> --help` for the full argument list of each
subcommand.


Modules
-------
    entry    -- shared contracts (DiscEntry, DiscSide, DiscImage, ...)
    boot     -- BootOption enum
    tokens   -- BBC BASIC II token table and constants
    inf      -- .inf sidecar file parser and formatter
    codec    -- "bbc" text codec registration
    dfs      -- DFS disc image reader and writer (.ssd, .dsd)
    adfs     -- ADFS disc image reader and writer (.adf, .adl)
    basic    -- tokenize, detokenize, content sniffers, escape
    pretty   -- operator spacing and anti-listing trap handling
    image    -- format dispatch (openImage, createImage)
    disc     -- high-level orchestration
    cli      -- command-line interface
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("beebtools")
except PackageNotFoundError:
    # Package is not installed (e.g. running directly from source tree)
    __version__ = "unknown"

from .basic import (
    basicProgramSize, compactLine, detokenize, decodeLineRef,
    tokenize, encodeLineRef,
    prettyPrint,
    looksLikeTokenizedBasic, looksLikePlainText,
    escapeNonAscii, unescapeNonAscii, hasEscapes,
)
from .boot import BootOption
from .entry import (
    DiscEntry, DiscCatalogue, DiscFile, DiscError, DiscFormatError,
    FileType, isBasicExecAddr,
)
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
    ADFS_ROOT_SECTOR,
)
from .image import openImage, createImage, DiscSide, DiscImage
from .inf import InfData, parseInf, formatInf
from .disc import (
    search, extractAll, buildImage, createImageFile,
    sortCatalogueEntries, classifyFileType,
    readCatalogue, CatalogueListing, CatalogueEntry,
    extractFile, ExtractedFile, addFile, addFileTo, qualifyDiscPath,
    writeBasicText, readBasicText, formatEntryInf,
    getTitle, setTitle, getBoot, setBoot, discInfo, DiscInfo,
    getFileAttribs, setFileAttribs, FileAttribs,
    deleteFile, renameFile, compactDisc, makeDirectory,
)
from .cli import main

__all__ = [
    # -------------------------------------------------------------------
    # Tier 1: high-level operations
    # -------------------------------------------------------------------
    "openImage",
    "createImage",
    "createImageFile",
    "readCatalogue",
    "extractFile",
    "extractAll",
    "search",
    "buildImage",
    "addFile",
    "deleteFile",
    "renameFile",
    "compactDisc",
    "makeDirectory",
    "getTitle",
    "setTitle",
    "getBoot",
    "setBoot",
    "discInfo",
    "getFileAttribs",
    "setFileAttribs",

    # -------------------------------------------------------------------
    # Tier 2: types
    # -------------------------------------------------------------------
    "DiscImage",
    "DiscSide",
    "DiscEntry",
    "DiscCatalogue",
    "DiscFile",
    "ExtractedFile",
    "CatalogueListing",
    "CatalogueEntry",
    "DiscInfo",
    "FileAttribs",
    "BootOption",
    "FileType",
    "DiscError",
    "DiscFormatError",

    # -------------------------------------------------------------------
    # Tier 3: BASIC transforms
    # -------------------------------------------------------------------
    "tokenize",
    "detokenize",
    "prettyPrint",
    "classifyFileType",
    "looksLikeTokenizedBasic",
    "looksLikePlainText",
    "escapeNonAscii",
    "unescapeNonAscii",
    "hasEscapes",
    "compactLine",
    "encodeLineRef",
    "decodeLineRef",

    # -------------------------------------------------------------------
    # Tier 4: format-specific
    # -------------------------------------------------------------------
    # DFS
    "DFSEntry",
    "DFSCatalogue",
    "DFSSide",
    "DFSImage",
    "DFSError",
    "DFSFormatError",
    "openDiscImage",
    "createDiscImage",
    "validateDfsName",
    "splitDfsPath",
    # ADFS
    "ADFSEntry",
    "ADFSCatalogue",
    "ADFSDirectory",
    "ADFSFreeSpaceMap",
    "ADFSSide",
    "ADFSImage",
    "ADFSError",
    "ADFSFormatError",
    "openAdfsImage",
    "createAdfsImage",
    "validateAdfsName",
    "ADFS_S_SECTORS",
    "ADFS_M_SECTORS",
    "ADFS_L_SECTORS",
    "ADFS_ROOT_SECTOR",
    # .inf sidecars
    "parseInf",
    "formatInf",
    "formatEntryInf",
    "InfData",
    # Low-level disc helpers
    "addFileTo",
    "qualifyDiscPath",
    "sortCatalogueEntries",
    "writeBasicText",
    "readBasicText",
    "isBasicExecAddr",
    "registerCodec",

    # -------------------------------------------------------------------
    # CLI entry point
    # -------------------------------------------------------------------
    "main",
]
