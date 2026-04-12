# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""High-level disc operations.

Orchestration module that composes the lower-level modules into
coherent disc-wide operations. All operations work through the
DiscSide and DiscImage abstract base classes defined in entry.py, so
the same code handles both DFS and ADFS formats without branching.

Collaborators:
    boot   -- BootOption enum (Contracts)
    entry  -- DiscEntry / DiscSide / DiscImage ABCs, DiscFile transport (Contracts)
    basic  -- BASIC facade: tokenize, detokenize, classify, escape (BASIC)
    image  -- openImage / createImage dispatchers (Dispatch)
    disc   -- cross-module orchestration (this module, Orchestration)
    cli    -- argument parsing, output formatting (CLI)

All operations that span more than one of these belong here.
"""

import os
import re
import warnings as _warnings
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple, Union

from .boot import BootOption
from .entry import DiscEntry, DiscFile, DiscError, FileType, isBasicExecAddr
from .shared import BeebToolsWarning
from .basic import (
    basicProgramSize, compactLine, detokenize, tokenize,
    escapeNonAscii, unescapeNonAscii, hasEscapes,
    looksLikeTokenizedBasic, looksLikePlainText, prettyPrint,
)
from .image import DiscImage, DiscSide, createImage, openImage
from .inf import formatInf, parseInf, InfData, INF_X_START_SECTOR


# Characters that are illegal in Windows filenames, used when building
# safe output paths from DFS names.
_WINDOWS_ILLEGAL = set('\\/:*?"<>|')


def _sanitizeForFilesystem(raw: str) -> str:
    """Sanitize a raw string for use as a filesystem path component.

    Characters illegal on Windows are replaced with _xNN_ hex encoding
    to guarantee uniqueness - two distinct illegal source characters will
    never produce the same output. Control characters are dropped.
    """
    parts = []
    for ch in raw:
        if ord(ch) < 0x20:
            # Drop control characters.
            continue
        if ch in _WINDOWS_ILLEGAL:
            # Encode as _xNN_ so each illegal char maps to a unique string.
            parts.append(f"_x{ord(ch):02X}_")
        else:
            parts.append(ch)
    return "".join(parts)


def sanitizeDfsDir(dfs_dir: str) -> str:
    """Sanitize a DFS directory character for use as a filesystem directory.

    Most DFS directory characters ('$', 'T', etc.) are safe on all
    platforms and pass through unchanged. Characters illegal on Windows
    are replaced with _xNN_ hex encoding.

    Examples:
        '$'  -> '$'
        'T'  -> 'T'
        '/'  -> '_x2F_'

    Args:
        dfs_dir: Single-character DFS directory prefix (e.g. 'T', '$').

    Returns:
        Safe directory name.
    """
    return _sanitizeForFilesystem(dfs_dir)


def sanitizeDfsFilename(dfs_name: str) -> str:
    """Sanitize a DFS filename for use as a filesystem filename.

    Characters illegal on Windows are replaced with _xNN_ hex encoding.
    Control characters are dropped.

    Examples:
        'MYPROG' -> 'MYPROG'
        'A/B'    -> 'A_x2F_B'  (slash encoded)
        'A\\B'   -> 'A_x5C_B'  (backslash encoded, distinct)

    Args:
        dfs_name: DFS filename, up to 7 characters (e.g. 'MYPROG').

    Returns:
        Safe filename with no extension.
    """
    return _sanitizeForFilesystem(dfs_name)


def sanitizeEntryPath(directory: str, name: str) -> Tuple[str, str]:
    """Sanitize a directory path and filename for filesystem output.

    Handles both flat DFS directories (single character like '$') and
    hierarchical ADFS paths (e.g. '$.GAMES'). Directory components
    are split on '.' and each is individually sanitized.

    Args:
        directory: Directory from a catalogue entry.
        name:      Filename from the entry.

    Returns:
        Tuple of (safe_directory_path, safe_filename).
    """
    dir_parts = directory.split(".")
    safe_dir = os.path.join(*[_sanitizeForFilesystem(p) for p in dir_parts])
    safe_name = _sanitizeForFilesystem(name)
    return safe_dir, safe_name


def resolveOutputPath(
    out_dir: str,
    disc_side: int,
    safe_dir: str,
    safe_name: str,
    multi_side: bool,
) -> str:
    """Resolve the output path for one file during bulk extraction.

    Builds a hierarchical path using the DFS directory character as a
    real filesystem subdirectory:
        - Single-sided: out_dir/dir/filename
        - Double-sided: out_dir/sideN/dir/filename

    All intermediate directories are created automatically.

    Args:
        out_dir:    Root output directory.
        disc_side:  Side number (0 or 1) for this file.
        safe_dir:   Sanitized DFS directory (e.g. '$', 'T').
        safe_name:  Sanitized DFS filename (e.g. 'MYPROG').
        multi_side: True when the image has more than one side.

    Returns:
        Path string to write the file to (extension not included).
    """
    if multi_side:
        dir_path = os.path.join(out_dir, f"side{disc_side}", safe_dir)
    else:
        dir_path = os.path.join(out_dir, safe_dir)

    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, safe_name)


# -----------------------------------------------------------------------
# Path qualification
# -----------------------------------------------------------------------

def formatEntryInf(entry: DiscEntry) -> str:
    """Format a catalogue entry as a .inf sidecar line.

    Convenience wrapper over formatInf() that destructures a DiscEntry
    into the positional arguments formatInf() expects. Lives in disc.py
    rather than inf.py because inf.py is a Contracts-layer leaf module
    and must not know about DiscEntry.

    Writes the experimental ``X_START_SECTOR=<n>`` extra field when the
    entry carries a start sector. This is the mechanism that lets
    ``buildImage`` later rebuild the disc with byte-exact on-disc file
    placement, which is required for round-tripping copy-protected
    discs that declare overlapping sector allocations.
    """

    extras = {}

    if entry.start_sector is not None:
        extras[INF_X_START_SECTOR] = str(entry.start_sector)

    return formatInf(
        entry.directory, entry.name,
        entry.load_addr, entry.exec_addr,
        entry.length, access_byte=entry.accessByte,
        extra_info=extras if extras else None,
    )


def qualifyDiscPath(path: str) -> str:
    """Normalise a user-supplied path to a fully-qualified disc path.

    DFS: 'MYPROG' -> '$.MYPROG', 'T.MYPROG' passes through.
    ADFS: '$.DIR.FILE' passes through, 'DIR.FILE' -> '$.DIR.FILE'.

    Args:
        path: User-supplied disc filename.

    Returns:
        Fully-qualified disc path with directory prefix.
    """
    if len(path) >= 3 and path[1] == ".":
        return path
    return f"$.{path}"


# -----------------------------------------------------------------------
# Text-mode helpers for BASIC extraction/build round-tripping
# -----------------------------------------------------------------------

def writeBasicText(
    path: str,
    lines: List[str],
    text_mode: str,
) -> None:
    """Write detokenized BASIC lines to a text file.

    text_mode controls how non-ASCII bytes (e.g. teletext control codes)
    are represented:
        'ascii'  -- replace with '?' (lossy, maximum compatibility)
        'utf8'   -- write as UTF-8 (lossless, modern editors)
        'escape' -- \\xHH notation (lossless, plain ASCII, round-trips)
    """
    if text_mode == "utf8":
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    elif text_mode == "escape":
        escaped = [escapeNonAscii(line) for line in lines]
        with open(path, "w", encoding="ascii") as f:
            f.write("\n".join(escaped) + "\n")
    else:
        # Default: ASCII with replacement.
        with open(path, "w", encoding="ascii", errors="replace") as f:
            f.write("\n".join(lines) + "\n")


def readBasicText(data: bytes) -> List[str]:
    """Read a .bas file's bytes and return lines, unescaping if needed.

    Detects escape mode by looking for \\xHH sequences.  Otherwise tries
    UTF-8, falling back to ASCII with replacement.
    """
    # Try UTF-8 first (covers both utf8 and escape modes).
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("ascii", errors="replace")

    lines = text.splitlines()

    # If any line contains \xHH escapes, unescape them all.
    if hasEscapes(text):
        lines = [unescapeNonAscii(line) for line in lines]

    return lines


# -----------------------------------------------------------------------
# File classification
# -----------------------------------------------------------------------

def classifyFileType(entry: DiscEntry, data: bytes) -> FileType:
    """Classify a disc file by inspecting its metadata and content.

    Combines the catalogue entry's exec-address metadata
    (`entry.isBasic`) with content sniffers from the BASIC layer
    (`looksLikeTokenizedBasic`, `basicProgramSize`,
    `looksLikePlainText`) to produce a file-level judgment.

    Two branches can return `FileType.BASIC_ISH`, for opposite
    reasons:

        1. Standard BASIC exec address, but the bytes do not parse
           as tokenized BASIC. Usually a corrupt file or a
           hand-authored file with a BASIC exec by mistake.

        2. Non-BASIC exec address, but the bytes ARE valid
           tokenized BASIC. Usually a deliberately-marked
           "include" file produced with `*SAVE` and explicit
           addresses, meant to be loaded with `LOAD` or merged
           into another program rather than run directly.

    See the `FileType` class docstring in `entry.py` for the full
    semantics of each classification.

    Args:
        entry: Catalogue entry with metadata (isBasic, isDirectory, etc.).
        data:  Raw file content bytes.

    Returns:
        A FileType enum member.
    """
    # Branch 1: exec address says BASIC.
    if entry.isBasic:
        if looksLikeTokenizedBasic(data):
            prog_size = basicProgramSize(data)
            if prog_size < len(data) - 16:
                return FileType.BASIC_MC
            return FileType.BASIC
        # Exec claims BASIC but content is not tokenized.
        return FileType.BASIC_ISH

    # Branch 2: exec address is not BASIC. Fall back to content.
    if looksLikeTokenizedBasic(data):
        prog_size = basicProgramSize(data)
        if prog_size < len(data) - 16:
            return FileType.BASIC_MC
        # Tokenized BASIC with a non-standard exec - an "include".
        return FileType.BASIC_ISH

    if looksLikePlainText(data):
        return FileType.TEXT

    return FileType.BINARY


# -----------------------------------------------------------------------
# Single-file extraction
# -----------------------------------------------------------------------

@dataclass
class ExtractedFile:
    """A file extracted from a disc image with post-processing applied.

    This is the read-path counterpart to DiscFile (the write-path
    transport).  DiscFile carries raw content going in; ExtractedFile
    carries classified, potentially detokenized content coming out.

    See architecture.md 'File Data Types' for why DiscEntry, DiscFile,
    and ExtractedFile are separate types.

    Attributes:
        file_type:  FileType enum member (BASIC, BASIC_MC, BASIC_ISH,
                    TEXT, or BINARY). See the FileType class docstring
                    in entry.py for full semantics.
        data:       Raw file bytes (always populated).
        lines:      Detokenized BASIC lines (None for non-BASIC files).
        entry:      The matched catalogue entry.
        side:       Disc side number the file was found on.
        basic_size: Size of the BASIC program portion (BASIC_MC only).
    """
    file_type: FileType
    data: bytes
    lines: Optional[List[str]]
    entry: DiscEntry
    side: int
    basic_size: Optional[int] = None


def extractFile(
    image_path: str,
    filename: str,
    pretty: bool = False,
    text_mode: str = "ascii",
) -> ExtractedFile:
    """Extract a single file from a disc image by name.

    Handles file lookup across sides, ambiguity resolution, BASIC
    detection, hybrid (BASIC+MC) detection, detokenization, and
    pretty-printing. Returns a structured result - does not write
    to the filesystem or stdout.

    Args:
        image_path: Path to a disc image (.ssd, .dsd, .adf, or .adl).
        filename:   File to extract (e.g. 'T.MYPROG', '$.GAMES.ELITE',
                    or bare 'MYPROG').
        pretty:     Apply pretty-printer spacing to BASIC output.
        text_mode:  Encoding for BASIC text ('ascii', 'utf8', 'escape').

    Returns:
        ExtractedFile with the file data and metadata.

    Raises:
        DiscError: If file not found or filename is ambiguous.
    """
    image = openImage(image_path)
    target = filename
    found = None

    # Try exact fullName match (handles "T.MYPROG" and "$.GAMES.ELITE").
    if "." in target:
        for disc in image.sides:
            catalogue = disc.readCatalogue()
            for e in catalogue.entries:
                if e.fullName.upper() == target.upper():
                    found = (disc, e)
                    break
            if found:
                break

    if not found:
        # Bare name search across all sides and directories.
        matches = []

        for disc in image.sides:
            catalogue = disc.readCatalogue()
            for e in catalogue.entries:
                if e.name.upper() == target.upper():
                    matches.append((disc, e))

        if len(matches) == 1:
            found = matches[0]
        elif len(matches) > 1:
            locations = ", ".join(
                f"Side {d.side}: {e.fullName}" for d, e in matches
            )
            raise DiscError(
                f"Ambiguous filename '{target}' - specify with full path. "
                f"Found: {locations}"
            )

    if not found:
        raise DiscError(f"File not found: {target}")

    disc, entry = found
    data = disc.readFile(entry)

    # Classify the file content.
    file_type = classifyFileType(entry, data)

    if file_type is FileType.BASIC:
        # Pure BASIC - detokenize and optionally pretty-print.
        text_lines = detokenize(data)
        if pretty:
            text_lines = prettyPrint(text_lines)
        return ExtractedFile(
            file_type=FileType.BASIC, data=data, lines=text_lines,
            entry=entry, side=disc.side,
        )

    if file_type is FileType.BASIC_MC:
        # Hybrid file - return raw binary to preserve machine code.
        prog_size = basicProgramSize(data)
        return ExtractedFile(
            file_type=FileType.BASIC_MC, data=data, lines=None,
            entry=entry, side=disc.side, basic_size=prog_size,
        )

    # BASIC_ISH, TEXT, or BINARY - return raw data.
    return ExtractedFile(
        file_type=file_type, data=data, lines=None,
        entry=entry, side=disc.side,
    )


# -----------------------------------------------------------------------
# Add file to disc image
# -----------------------------------------------------------------------

def addFile(
    image_path: str,
    spec: DiscFile,
    side: int = 0,
    retokenize: bool = False,
) -> DiscEntry:
    """Add a file to an existing disc image and write it back.

    Convenience wrapper around addFileTo() that handles the
    open + add + serialize + write-back lifecycle for the common
    single-file case. Use addFileTo() directly when adding multiple
    files to an in-memory image before a single serialize (see
    buildImage for that pattern).

    Args:
        image_path: Path to a disc image file.
        spec:       DiscFile with path, data, addresses, and lock flag.
        side:       Disc side (0 or 1, default 0).
        retokenize: If True, tokenize plain-text BASIC data before
                    adding to the disc.

    Returns:
        The new catalogue entry added to the disc.

    Raises:
        DiscError: If the file cannot be added.
    """
    image = openImage(image_path)

    entry = addFileTo(image, side, spec, retokenize=retokenize)

    _writeBack(image, image_path)

    return entry


def addFileTo(
    image: DiscImage,
    side_index: int,
    spec: DiscFile,
    retokenize: bool = False,
) -> DiscEntry:
    """Add a file to a disc image with optional retokenization.

    When retokenize is True and the file data is plain-text BASIC (not
    already tokenized), it is tokenized before adding to the disc.

    Args:
        image:       Open disc image.
        side_index:  Disc side number (0 or 1).
        spec:        DiscFile with path, data, addresses, and lock flag.
        retokenize:  If True, tokenize plain-text BASIC data.

    Returns:
        The new catalogue entry added to the disc.

    Raises:
        DiscError: If the file cannot be added.
    """
    data = spec.data

    # Retokenize plain-text BASIC if requested.
    if retokenize and not looksLikeTokenizedBasic(data) and looksLikePlainText(data):
        text = data.decode("ascii", errors="replace")
        text_lines = text.splitlines()
        data = tokenize(text_lines)
        # Rebuild spec with tokenized data.
        spec = DiscFile(
            path=spec.path, data=data,
            load_addr=spec.load_addr, exec_addr=spec.exec_addr,
            locked=spec.locked,
        )

    side = image.sides[side_index]
    return side.addFile(spec)


# -----------------------------------------------------------------------
# Sorting (format-agnostic)
# -----------------------------------------------------------------------

# -----------------------------------------------------------------------
# Catalogue read (display + optional content classification)
# -----------------------------------------------------------------------

@dataclass
class CatalogueEntry:
    """One entry in a catalogue listing, optionally classified.

    entry:     The underlying DiscEntry from the format engine.
    file_type: FileType enum member, or None when unclassified.
               Directories always carry None here - callers check
               entry.isDirectory. See the FileType class docstring
               in entry.py for the full set of classifications.
    """
    entry: DiscEntry
    file_type: Optional[FileType]


@dataclass
class CatalogueListing:
    """Catalogue listing for one disc side.

    Returned by readCatalogue() as part of a list (one per side).
    Holds everything a CLI or library caller needs to render a listing
    without touching the format engine directly.
    """
    side: int
    title: str
    boot_option: BootOption
    tracks: int
    entry_count: int
    entries: List[CatalogueEntry]


def readCatalogue(
    image_path: str,
    sort_mode: str = "name",
    inspect: bool = False,
) -> List[CatalogueListing]:
    """Read the catalogue of every side of a disc image.

    Performs the full catalogue-listing orchestration in one call so
    CLI and library callers never need to open the image, iterate
    sides, read catalogues, sort entries, or classify files themselves.

    When inspect is True, each non-directory file is read and run
    through classifyFileType() so the result carries a content-based
    FileType enum member. When inspect is False, the cheaper
    metadata-only BASIC detection via entry.isBasic is used and all
    other files report None.

    Args:
        image_path: Path to a disc image file.
        sort_mode:  Entry order: 'name' (default), 'catalog', or 'size'.
        inspect:    Read file content to classify each entry.

    Returns:
        One CatalogueListing per disc side, in physical side order.
    """
    image = openImage(image_path)

    listings: List[CatalogueListing] = []

    for disc in image.sides:
        cat = disc.readCatalogue()

        ordered = sortCatalogueEntries(cat.entries, sort_mode)

        classified: List[CatalogueEntry] = []

        for e in ordered:
            if e.isDirectory:
                # Directories carry no file classification; CLI layer
                # renders them separately based on entry.isDirectory.
                classified.append(CatalogueEntry(entry=e, file_type=None))
                continue

            if inspect:
                # Content inspection - read the file and classify.
                data = disc.readFile(e)
                tag = classifyFileType(e, data)
                classified.append(CatalogueEntry(entry=e, file_type=tag))
                continue

            # Metadata-only: trust the BASIC exec address.
            tag = FileType.BASIC if e.isBasic else None
            classified.append(CatalogueEntry(entry=e, file_type=tag))

        listings.append(CatalogueListing(
            side=disc.side,
            title=cat.title,
            boot_option=cat.boot_option,
            tracks=cat.tracks,
            entry_count=len(cat.entries),
            entries=classified,
        ))

    return listings


def sortCatalogueEntries(
    entries: Sequence[DiscEntry], sort_mode: str
) -> List[DiscEntry]:
    """Return catalogue entries in the requested display order.

    Args:
        entries:   Sequence of DiscEntry-compatible objects.
        sort_mode: One of 'name', 'catalog', or 'size'.
            name    -- alphabetical by full path (case-insensitive)
            catalog -- original on-disc catalogue order
            size    -- ascending file length, then alphabetical

    Returns:
        New list in the requested order.
    """
    if sort_mode == "catalog":
        return list(entries)

    if sort_mode == "size":
        return sorted(entries, key=lambda e: (e.length, e.fullName.upper()))

    # Default: alphabetical by full path.
    return sorted(entries, key=lambda e: e.fullName.upper())


# -----------------------------------------------------------------------
# Search
# -----------------------------------------------------------------------

def search(
    image_path: str,
    pattern: str,
    filename: Optional[str] = None,
    ignore_case: bool = False,
    pretty: bool = False,
    use_regex: bool = False,
) -> List[Dict[str, Union[str, int]]]:
    """Search all BASIC files on a disc for lines matching a text pattern.

    Each BASIC file is detokenized and each line is scanned for the pattern.
    Non-BASIC files are skipped. Results preserve disc order.

    Args:
        image_path:  Path to a .ssd or .dsd disc image.
        pattern:     Text to search for. Treated as a literal string unless
                     use_regex is True.
        filename:    If given, only search this file (e.g. 'T.MYPROG' or bare 'MYPROG').
        ignore_case: Case-insensitive match when True.
        pretty:      Apply pretty-printer spacing before matching.
        use_regex:   Treat pattern as a Python regular expression. If False
                     (default), the pattern is matched literally.

    Returns:
        List of match dicts, each with keys:
            'side'        -- int: disc side number
            'filename'    -- str: DFS filename, e.g. 'T.MYPROG'
            'line_number' -- int: BBC BASIC line number
            'line'        -- str: full detokenized line text

    Raises:
        re.error: If use_regex is True and pattern is not a valid regex.
    """
    flags = re.IGNORECASE if ignore_case else 0
    raw = pattern if use_regex else re.escape(pattern)
    compiled = re.compile(raw, flags)

    results = []

    image = openImage(image_path)

    for side in image:
        for entry in side:
            # Skip directory entries (ADFS directories are containers, not files).
            if entry.isDirectory:
                continue

            # Scope to a specific file when requested.
            if filename is not None:
                if entry.fullName != filename and entry.name != filename:
                    continue

            if not entry.isBasic:
                continue

            data = side.readFile(entry)
            if not looksLikeTokenizedBasic(data):
                continue

            text_lines = detokenize(data)
            if pretty:
                text_lines = prettyPrint(text_lines)

            for line in text_lines:
                # Lines are formatted as a 5-char right-justified number + content.
                # Search the content part only, not the leading line number.
                content = line[5:]
                if compiled.search(content):
                    results.append({
                        "side": side.side,
                        "filename": entry.fullName,
                        "line_number": int(line[:5]),
                        "line": line,
                    })

    return results


def extractAll(
    image_path: str,
    out_dir: str,
    pretty: bool = False,
    write_inf: bool = False,
    text_mode: str = "ascii",
) -> List[Dict[str, Union[str, int]]]:
    """Extract every file from a disc image into a directory.

    BASIC programs are saved as .bas plain text files.
    Plain text files are saved as .txt with CR normalised to LF.
    Binary files are saved as .bin raw bytes.

    Files are laid out hierarchically with the DFS directory character
    as a real subdirectory. On double-sided images, an additional
    side0/ or side1/ level is added:
        - SSD: out_dir/$/BOOT.bas
        - DSD: out_dir/side0/$/BOOT.bas

    When write_inf is True, a .inf sidecar file is written alongside
    every extracted file, preserving the DFS load address, exec address,
    length, and lock flag in the standard community interchange format.

    Args:
        image_path: Path to a .ssd or .dsd disc image.
        out_dir:    Directory to write extracted files into. Created if absent.
        pretty:     Apply pretty-printer spacing to BASIC output when True.
        write_inf:  Write .inf sidecar files alongside extracted files.
        text_mode:  How non-ASCII bytes in BASIC strings are written:
                    'ascii'  -- replace with '?' (lossy, default)
                    'utf8'   -- write as UTF-8 (lossless)
                    'escape' -- \\xHH notation (lossless, plain ASCII)

    Returns:
        List of result dicts, one per extracted file. Each dict has:
            'type' -- str: 'BASIC', 'text', or 'binary'
            'path' -- str: output file path written
        Binary results also include:
            'load'   -- int: load address
            'exec'   -- int: exec address
            'length' -- int: file length in bytes
    """
    os.makedirs(out_dir, exist_ok=True)

    image = openImage(image_path)
    multi_side = len(image) > 1

    results = []

    for side in image:
        for entry in side:
            # Skip directory entries (ADFS directories are containers, not files).
            if entry.isDirectory:
                continue

            safe_dir, safe_name = sanitizeEntryPath(entry.directory, entry.name)
            stem = resolveOutputPath(out_dir, side.side, safe_dir, safe_name, multi_side)
            data = side.readFile(entry)

            if entry.isBasic and looksLikeTokenizedBasic(data):
                # Check whether the BASIC program occupies the whole file,
                # or whether there is appended machine code after it.
                # Files with trailing binary data (e.g. BASIC loader +
                # 6502 game engine) must be kept as binary to preserve
                # the machine code.
                prog_size = basicProgramSize(data)
                has_trailing_binary = prog_size < len(data) - 16

                if has_trailing_binary:
                    # BASIC + machine code hybrid - save as binary.
                    out_path = stem + ".bin"
                    with open(out_path, "wb") as f:
                        f.write(data)
                    results.append({
                        "type": "BASIC+MC",
                        "path": out_path,
                        "load": entry.load_addr,
                        "exec": entry.exec_addr,
                        "length": entry.length,
                        "basic_size": prog_size,
                    })
                else:
                    # Pure BASIC - detokenize and write as plain text.
                    out_path = stem + ".bas"
                    text_lines = detokenize(data)
                    if pretty:
                        text_lines = prettyPrint(text_lines)
                    writeBasicText(out_path, text_lines, text_mode)
                    results.append({"type": "BASIC", "path": out_path})

            elif looksLikePlainText(data):
                # Plain ASCII text file - save as .txt.
                # BBC text editors use CR (0x0D) only as a line terminator.
                # Normalise to Unix LF so the output file is portable.
                out_path = stem + ".txt"
                text = data.decode("ascii", errors="replace")
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                with open(out_path, "w", encoding="ascii", errors="replace") as f:
                    f.write(text)
                results.append({"type": "text", "path": out_path})

            else:
                # Write binary file.
                out_path = stem + ".bin"
                with open(out_path, "wb") as f:
                    f.write(data)
                results.append({
                    "type": "binary",
                    "path": out_path,
                    "load": entry.load_addr,
                    "exec": entry.exec_addr,
                    "length": entry.length,
                })

            # Write .inf sidecar alongside the data file if requested.
            if write_inf:
                inf_line = formatEntryInf(entry)
                with open(out_path + ".inf", "w", encoding="utf-8") as f:
                    f.write(inf_line + "\n")

    return results


def createImageFile(
    output_path: str,
    tracks: int = 80,
    title: str = "",
    boot_option: BootOption = BootOption.OFF,
) -> int:
    """Create a blank formatted disc image and write it to disk.

    Use this to create a new disc image file on disk directly, unlike
    `image.createImage` that creates an in-memory image only.

    The format is determined by the output_path extension:
        .ssd  -- DFS single-sided
        .dsd  -- DFS double-sided interleaved
        .adf  -- ADFS (40-track: 160K, 80-track: 320K)
        .adl  -- ADFS-L 640K

    Args:
        output_path: Path for the new disc image. Extension sets format.
        tracks:      Number of tracks (40 or 80).
        title:       Disc title (format-specific length limit).
        boot_option: Boot option to record in the catalogue.

    Returns:
        The size in bytes of the image written to disk.
    """
    image = createImage(
        output_path, tracks=tracks, title=title, boot_option=boot_option,
    )

    image_bytes = image.serialize()

    with open(output_path, "wb") as f:
        f.write(image_bytes)

    return len(image_bytes)


def buildImage(
    source_dir: str,
    output_path: str,
    tracks: int = 80,
    title: str = "",
    boot_option: BootOption = BootOption.OFF,
    save: bool = False,
) -> bytes:
    """Build a disc image from a directory of files with .inf sidecars.

    The disc format is determined by the output_path extension:
        .ssd  -- DFS single-sided
        .dsd  -- DFS double-sided interleaved
        .adf  -- ADFS (40-track: 160K, 80-track: 320K)
        .adl  -- ADFS-L 640K

    The source directory is expected to follow the hierarchical layout
    produced by extractAll. For DFS: one subdirectory per directory
    character (e.g. '$/', 'T/'). For DSD: side0/ and side1/
    subdirectories. For ADFS: a '$' root directory.

    Files without a .inf sidecar are skipped with a warning. BASIC
    programs compacted to fit the 255-byte line limit also emit
    warnings. Warnings are emitted via the standard warnings module
    as BeebToolsWarning. Callers who need to capture them
    programmatically can use warnings.catch_warnings(record=True).

    Args:
        source_dir:  Path to the root directory of extracted files.
        output_path: Path whose extension determines the disc format.
                     When save is True this is also the destination file.
        tracks:      Number of tracks (40 or 80).
        title:       Disc title (up to 12 characters).
        boot_option: Boot option (0-3).
        save:        When True, the assembled image is also written to
                     output_path. Default False, in which case no file
                     is written and the caller is responsible for
                     persisting the returned bytes.

    Returns:
        The assembled disc image as bytes. When save is True the same
        bytes have also been written to output_path.

    Raises:
        DiscError: If a file cannot be added (name conflict, disc full, etc.).
        DFSFormatError: If the output_path extension is unrecognised.
        OSError:   If save is True and the write to output_path fails.
    """
    image = createImage(
        output_path, tracks=tracks, title=title, boot_option=boot_option,
    )

    if len(image) > 1:
        for i, side in enumerate(image):
            side_path = os.path.join(source_dir, f"side{i}")
            _walkSourceTree(side, side_path)
    else:
        _walkSourceTree(image[0], source_dir)

    image_bytes = image.serialize()

    if save:
        with open(output_path, "wb") as f:
            f.write(image_bytes)

    return image_bytes


def _collectSourceFiles(
    fs_dir: str,
) -> List[Tuple[str, str, InfData]]:
    """Walk a filesystem tree and collect (fs_path, fs_leaf, inf) tuples.

    This is the read-only first half of the build pipeline: it only
    visits files and reads their .inf sidecars, so no disc mutation
    happens until the caller applies the collected records. Directories
    on the filesystem are descended into but their names are not
    retained - the true disc layout comes from the .inf sidecars, not
    from the (possibly sanitised) filesystem names.

    Args:
        fs_dir: Root filesystem directory to walk.

    Returns:
        List of (fs_path, fs_leaf_name, InfData) tuples, sorted by
        disc path so output is deterministic.
    """

    collected: List[Tuple[str, str, InfData]] = []

    def _visit(current: str) -> None:
        """Depth-first walk of a filesystem directory."""
        if not os.path.isdir(current):
            return

        for entry in sorted(os.listdir(current)):
            # Skip .inf sidecar files - they are read alongside their data file.
            if entry.endswith(".inf"):
                continue

            path = os.path.join(current, entry)

            if os.path.isdir(path):
                _visit(path)
                continue

            if not os.path.isfile(path):
                continue

            inf_path = path + ".inf"
            if not os.path.isfile(inf_path):
                _warnings.warn(
                    f"no .inf sidecar for {path}, skipping",
                    BeebToolsWarning,
                    stacklevel=4,
                )
                continue

            with open(inf_path, "r", encoding="utf-8") as handle:
                inf_line = handle.readline().rstrip("\r\n")

            try:
                inf = parseInf(inf_line)
            except ValueError as exc:
                _warnings.warn(
                    f"bad .inf sidecar {inf_path}: {exc}",
                    BeebToolsWarning,
                    stacklevel=4,
                )
                continue

            collected.append((path, entry, inf))

    _visit(fs_dir)

    # Sort by disc path so directory creation is deterministic.
    collected.sort(key=lambda rec: rec[2].fullName)

    return collected


def _ensureAdfsDirs(side: DiscSide, dir_paths: List[str]) -> None:
    """Auto-create every ADFS directory referenced by the build set.

    Takes the set of parent directories mentioned by the .inf records
    being placed on disc and creates each one (shortest first) unless
    it is the root or already exists. Non-ADFS sides are a no-op
    because DFS directories are implicit.

    Args:
        side:      The DiscSide to create directories on.
        dir_paths: Unsorted list of ADFS parent directory paths such
                   as ``$``, ``$.GAMES``, ``$.GAMES.ACTION``.
    """

    # Collect every path prefix we might need, de-duplicated. A file
    # under "$.A.B.C" requires "$.A", "$.A.B", and "$.A.B.C" to exist.
    # Directory paths that do not start with the $ root are DFS-style
    # single-character tokens and are skipped entirely because DFS has
    # no explicit directory structure on disc.
    prefixes = set()

    for dir_path in dir_paths:
        if not dir_path or dir_path == "$":
            continue

        if not dir_path.startswith("$"):
            continue

        parts = dir_path.split(".")[1:]

        accumulated = "$"
        for segment in parts:
            accumulated = f"{accumulated}.{segment}"
            prefixes.add(accumulated)

    # Create shortest paths first so parents exist before children.
    # Any DiscError from mkdir (e.g. "subdirectories not supported" on
    # a DFS side, or a pre-existing directory of the same name) aborts
    # the loop because the remaining paths are either unreachable or
    # already in place.
    for path in sorted(prefixes, key=lambda p: (p.count("."), p)):
        try:
            side.mkdir(path)
        except DiscError:
            return


def _walkSourceTree(
    side: DiscSide,
    fs_dir: str,
    disc_parent: str = "",
) -> None:
    """Build a disc side from a filesystem tree of data + .inf sidecars.

    File metadata (disc path, load/exec addresses, lock flag, and
    optional on-disc start sector) comes from the .inf sidecar next to
    each data file. The filesystem directory names are irrelevant -
    the disc layout is reconstructed entirely from the .inf records,
    so sanitised filesystem names like ``T_x3E_D`` correctly map back
    to the original disc name ``T>D``.

    Processing happens in several passes:

    1. Collect every (data file, .inf record) pair from the filesystem.
    2. Auto-create any ADFS directories referenced by the collected
       records.
    3. Split the records into placed (those whose .inf sidecar carries
       an ``X_START_SECTOR`` / ``START_SECTOR`` hint) and unplaced.
    4. Write the placed records first, ordered by end sector ascending
       so that any last-writer-wins conflict at a shared sector lands
       on the file that fully covers the sector. This preserves
       byte-exact round-tripping of copy-protected discs that declare
       overlapping sector allocations (Level 9 games).
    5. Write the unplaced records using the format engine's normal
       free-space allocator.

    DFS directories (e.g. ``$``, ``T``, ``+``) are implicit - the
    directory letter is stored as metadata in each file's catalogue
    entry, so no mkdir step is needed.

    Args:
        side:        A DiscSide to add files and directories to.
        fs_dir:      Filesystem directory to walk.
        disc_parent: Unused; retained for backward compatibility with
                     the previous recursive signature.
    """

    # Pass 1: collect every source file with its .inf metadata.
    records = _collectSourceFiles(fs_dir)

    # Auto-create every ADFS parent directory referenced by the records.
    _ensureAdfsDirs(side, [rec[2].directory for rec in records])

    # Split into placed and unplaced, preserving the original order
    # for the unplaced bucket. Placed records are sorted by end
    # sector ascending so that the file whose footprint extends
    # furthest writes last and its bytes win in any overlap region.
    SECTOR_SIZE = 256
    placed: List[Tuple[str, str, InfData, int, int]] = []
    unplaced: List[Tuple[str, str, InfData]] = []

    for path, fs_leaf, inf in records:
        start = inf.startSector

        if start is None:
            unplaced.append((path, fs_leaf, inf))
            continue

        length = (
            inf.length if inf.length is not None else os.path.getsize(path)
        )
        sectors = max(1, (length + SECTOR_SIZE - 1) // SECTOR_SIZE)
        end_sector = start + sectors - 1
        placed.append((path, fs_leaf, inf, start, end_sector))

    placed.sort(key=lambda item: (item[4], -item[3]))

    def addOne(
        path: str,
        fs_leaf: str,
        inf: InfData,
        start_sector: Optional[int],
    ) -> None:
        """Load one source file and add it to the disc side."""

        with open(path, "rb") as handle:
            data = handle.read()

        # Retokenize .bas files back to BBC BASIC binary format. The
        # extract step detokenizes BASIC programs into plain text,
        # which is larger than the tokenized form. Re-tokenizing here
        # restores the compact binary representation so the rebuilt
        # disc image does not overflow.
        if fs_leaf.endswith(".bas") and isBasicExecAddr(inf.exec_addr):
            lines = readBasicText(data)

            # Tokenize with auto-compaction for overflowing lines.
            # Pretty-printed whitespace can push dense lines past the
            # 255-byte limit. The on_overflow callback compacts just
            # the offending line and collects a warning.
            compact_warnings: List[str] = []

            def compactAndWarn(text: str, msg: str) -> str:
                """Compact a line and record the overflow warning."""
                compact_warnings.append(msg)
                return compactLine(text)

            try:
                data = tokenize(lines, on_overflow=compactAndWarn)
            except ValueError as exc:
                raise DiscError(
                    f"side {side.side} {inf.fullName}: {exc}"
                ) from exc

            for w in compact_warnings:
                _warnings.warn(
                    f"side {side.side} {inf.fullName}: "
                    f"compacted to fit ({w})",
                    BeebToolsWarning,
                    stacklevel=2,
                )

        side.addFile(DiscFile(
            path=inf.fullName,
            data=data,
            load_addr=inf.load_addr,
            exec_addr=inf.exec_addr,
            locked=inf.locked,
            start_sector=start_sector,
        ))

    # Pass 4: write placed files in end-sector ascending order.
    for path, fs_leaf, inf, _start, _end in placed:
        addOne(path, fs_leaf, inf, inf.startSector)

    # Pass 5: write unplaced files using normal allocation.
    for path, fs_leaf, inf in unplaced:
        addOne(path, fs_leaf, inf, None)


# ===================================================================
# In-place disc mutation helpers
# ===================================================================

def _writeBack(image: DiscImage, path: str) -> None:
    """Serialize a modified image back to its file.

    Args:
        image: The in-memory disc image (already mutated).
        path:  The file path to write to.
    """
    with open(path, "wb") as f:
        f.write(image.serialize())


# -------------------------------------------------------------------
# Title
# -------------------------------------------------------------------

def getTitle(image_path: str, side: int = 0) -> str:
    """Read the disc title from an image file.

    Args:
        image_path: Path to a disc image file.
        side:       Disc side (0 or 1, default 0).

    Returns:
        The disc title string.
    """
    image = openImage(image_path)
    cat = image[side].readCatalogue()

    return cat.title


def setTitle(image_path: str, title: str, side: int = 0) -> None:
    """Set the disc title on an existing image file.

    Validates the title length for the detected format and writes the
    updated catalogue back to disc.

    Args:
        image_path: Path to a disc image file.
        title:      New disc title.
        side:       Disc side (0 or 1, default 0).

    Raises:
        DiscError: If the title exceeds the format's maximum length.
    """
    image = openImage(image_path)
    side_obj = image[side]
    cat = side_obj.readCatalogue()

    # Ask the format engine for its title length limit.
    max_len = side_obj.maxTitleLength

    if len(title) > max_len:
        raise DiscError(
            f"Title too long: {len(title)} characters "
            f"(maximum {max_len} for this format)"
        )

    updated = replace(cat, title=title)
    side_obj.writeCatalogue(updated)

    _writeBack(image, image_path)


# -------------------------------------------------------------------
# Boot option
# -------------------------------------------------------------------

def getBoot(image_path: str, side: int = 0) -> BootOption:
    """Read the boot option from an image file.

    Args:
        image_path: Path to a disc image file.
        side:       Disc side (0 or 1, default 0).

    Returns:
        The current BootOption value.
    """
    image = openImage(image_path)
    cat = image[side].readCatalogue()

    return cat.boot_option


def setBoot(image_path: str, boot_option: BootOption, side: int = 0) -> None:
    """Set the boot option on an existing image file.

    Args:
        image_path:  Path to a disc image file.
        boot_option: New boot option value.
        side:        Disc side (0 or 1, default 0).
    """
    image = openImage(image_path)
    side_obj = image[side]
    cat = side_obj.readCatalogue()

    updated = replace(cat, boot_option=boot_option)
    side_obj.writeCatalogue(updated)

    _writeBack(image, image_path)


# -------------------------------------------------------------------
# Disc summary
# -------------------------------------------------------------------

@dataclass
class DiscInfo:
    """Summary of disc-level metadata returned by discInfo()."""

    title: str
    boot_option: BootOption
    free_space: int
    total_sectors: int
    tracks: int
    side: int


def discInfo(image_path: str, side: int = 0) -> DiscInfo:
    """Return a summary of disc-level metadata.

    Args:
        image_path: Path to a disc image file.
        side:       Disc side (0 or 1, default 0).

    Returns:
        DiscInfo with title, boot option, free space, and geometry.
    """
    image = openImage(image_path)
    side_obj = image[side]
    cat = side_obj.readCatalogue()

    return DiscInfo(
        title=cat.title,
        boot_option=cat.boot_option,
        free_space=side_obj.freeSpace(),
        total_sectors=cat.disc_size,
        tracks=cat.tracks,
        side=side,
    )


# -------------------------------------------------------------------
# File attributes
# -------------------------------------------------------------------

@dataclass
class FileAttribs:
    """File attribute summary returned by getFileAttribs()."""

    fullName: str
    load_addr: int
    exec_addr: int
    length: int
    locked: bool


def getFileAttribs(
    image_path: str, filename: str, side: int = 0,
) -> FileAttribs:
    """Read the attributes of a file on a disc image.

    Args:
        image_path: Path to a disc image file.
        filename:   File path on the disc (e.g. '$.MYPROG').
        side:       Disc side (0 or 1, default 0).

    Returns:
        FileAttribs with name, addresses, length, and locked status.

    Raises:
        DiscError: If the file is not found.
    """
    image = openImage(image_path)
    side_obj = image[side]
    path = qualifyDiscPath(filename)

    # Look up the entry in the catalogue.
    cat = side_obj.readCatalogue()
    for entry in cat.entries:
        if entry.fullName == path:
            return FileAttribs(
                fullName=entry.fullName,
                load_addr=entry.load_addr,
                exec_addr=entry.exec_addr,
                length=entry.length,
                locked=entry.locked,
            )

    raise DiscError(f"File '{path}' not found")


def setFileAttribs(
    image_path: str,
    filename: str,
    side: int = 0,
    locked: Optional[bool] = None,
    load_addr: Optional[int] = None,
    exec_addr: Optional[int] = None,
) -> None:
    """Set file attributes on an existing disc image.

    Only the attributes that are not None are changed. The file's data
    is not moved - only the catalogue entry is updated.

    Args:
        image_path: Path to a disc image file.
        filename:   File path on the disc (e.g. '$.MYPROG').
        side:       Disc side (0 or 1, default 0).
        locked:     New locked status, or None to leave unchanged.
        load_addr:  New load address, or None to leave unchanged.
        exec_addr:  New exec address, or None to leave unchanged.

    Raises:
        DiscError: If the file is not found.
    """
    image = openImage(image_path)
    side_obj = image[side]
    path = qualifyDiscPath(filename)

    # Look up the entry in the catalogue.
    cat = side_obj.readCatalogue()
    target = None

    for entry in cat.entries:
        if entry.fullName == path:
            target = entry
            break

    if target is None:
        raise DiscError(f"File '{path}' not found")

    # Build the replacement fields dict - only include changed values.
    changes = {}

    if locked is not None:
        changes["locked"] = locked

    if load_addr is not None:
        changes["load_addr"] = load_addr

    if exec_addr is not None:
        changes["exec_addr"] = exec_addr

    if not changes:
        return

    updated = replace(target, **changes)
    side_obj.updateEntry(path, updated)

    _writeBack(image, image_path)


# -------------------------------------------------------------------
# Rename
# -------------------------------------------------------------------

def deleteFile(
    image_path: str,
    filename: str,
    side: int = 0,
) -> None:
    """Delete a file from an existing disc image.

    The filename is normalised with qualifyDiscPath() so bare names
    get a '$.' prefix. The image is written back to disk after the
    entry is removed.

    Args:
        image_path: Path to a disc image file.
        filename:   Name of the file to delete (e.g. 'T.MYPROG').
        side:       Disc side (0 or 1, default 0).

    Raises:
        DiscError: If the file is not found.
    """
    image = openImage(image_path)
    side_obj = image[side]

    path = qualifyDiscPath(filename)

    side_obj.deleteFile(path)

    _writeBack(image, image_path)


def renameFile(
    image_path: str,
    old_name: str,
    new_name: str,
    side: int = 0,
) -> None:
    """Rename a file on an existing disc image.

    Both names are normalised with qualifyDiscPath() so bare names
    get a '$.' prefix. The file data is not moved - only the catalogue
    entry is updated.

    Args:
        image_path: Path to a disc image file.
        old_name:   Current filename on the disc (e.g. 'T.MYPROG').
        new_name:   New filename (e.g. 'T.NEWNAME').
        side:       Disc side (0 or 1, default 0).

    Raises:
        DiscError: If the source is not found or the destination
                   already exists.
    """
    image = openImage(image_path)
    side_obj = image[side]

    old_path = qualifyDiscPath(old_name)
    new_path = qualifyDiscPath(new_name)

    side_obj.renameFile(old_path, new_path)

    _writeBack(image, image_path)


# -------------------------------------------------------------------
# compact
# -------------------------------------------------------------------

def compactDisc(
    image_path: str,
    side: int = 0,
) -> int:
    """Defragment a disc image by closing gaps between files.

    Files are packed toward the highest sectors so all free space is
    contiguous. Only DFS images support compaction - ADFS raises
    DiscError.

    Args:
        image_path: Path to a disc image file.
        side:       Disc side (0 or 1, default 0).

    Returns:
        Number of bytes freed by compaction (zero if already packed).

    Raises:
        DiscError: If the format does not support compaction.
    """
    image = openImage(image_path)
    side_obj = image[side]

    freed = side_obj.compact()

    _writeBack(image, image_path)

    return freed


# -------------------------------------------------------------------
# mkdir
# -------------------------------------------------------------------

def makeDirectory(
    image_path: str,
    path: str,
    side: int = 0,
) -> None:
    """Create a subdirectory on an existing disc image.

    Only ADFS images support subdirectories - DFS raises DiscError.
    The parent directory must already exist.

    Args:
        image_path: Path to a disc image file.
        path:       Full disc path for the new directory
                    (e.g. '$.GAMES' or '$.GAMES.ARCADE').
        side:       Disc side (0 or 1, default 0).

    Raises:
        DiscError: If the format does not support subdirectories
                   or the parent directory does not exist.
    """
    image = openImage(image_path)
    side_obj = image[side]

    side_obj.mkdir(path)

    _writeBack(image, image_path)
