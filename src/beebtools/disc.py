# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""High-level disc operations.

Orchestration layer that composes the lower-level modules into coherent
disc-wide operations. All operations work through Protocols defined in
image.py, so the same code handles both DFS and ADFS formats.

Layer responsibilities:
    boot       -- BootOption enum (Layer 0)
    entry      -- DiscEntry Protocol and DiscFile transport (Layer 0)
    image      -- DiscSide/DiscImage Protocols plus openImage/createImage (Layer 3)
    detokenize -- tokenized binary to plain-text transform (Layer 2)
    pretty     -- text post-processing (Layer 2)
    disc       -- cross-layer orchestration (this module, Layer 4)
    cli        -- argument parsing, output formatting (Layer 5)

All operations that span more than one lower layer belong here.
"""

import os
import re
from typing import Dict, List, Optional, Sequence, Tuple, Union

from .boot import BootOption
from .entry import DiscEntry, DiscFile, isBasicExecAddr
from .image import DiscSide, createImage, openImage
from .detokenize import basicProgramSize, detokenize
from .inf import formatInf, parseInf
from .pretty import prettyPrint
from .tokenize import tokenize


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
# Content detection (format-agnostic, moved from dfs.py)
# -----------------------------------------------------------------------

def looksLikeTokenizedBasic(data: bytes) -> bool:
    """True if data contains a structurally valid Wilson/Acorn BASIC program.

    Walks the tokenized line structure looking for the 0x0D 0xFF
    end-of-program marker that every valid BBC BASIC program contains.

    A first-byte-only check is not sufficient because plain-text files
    beginning with CR (0x0D) would false-positive.

    See https://www.bbcbasic.net/wiki/doku.php?id=format for the
    canonical format-detection algorithm.
    """
    if len(data) < 2 or data[0] != 0x0D:
        return False

    # Walk the line structure and check for the 0xFF end marker.
    prog_size = basicProgramSize(data)
    return prog_size >= 2 and data[prog_size - 1] == 0xFF


# Bytes acceptable in a plain-text file: printable ASCII plus common
# whitespace (tab, carriage return, line feed).
_PLAIN_TEXT_BYTES = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def looksLikePlainText(data: bytes) -> bool:
    """True if every byte is printable ASCII or common whitespace.

    Checks for printable ASCII (0x20-0x7E) plus tab (0x09), line feed
    (0x0A), and carriage return (0x0D). An empty file is not plain text.
    """
    if not data:
        return False
    return all(b in _PLAIN_TEXT_BYTES for b in data)





# -----------------------------------------------------------------------
# Text-mode helpers for BASIC extraction/build round-tripping
# -----------------------------------------------------------------------

# Regex matching a \xHH escape sequence (two uppercase hex digits).
_ESCAPE_RE = re.compile(r"\\x([0-9A-F]{2})")


def escapeNonAscii(line: str) -> str:
    """Replace non-printable-ASCII characters with \\xHH escapes.

    Characters outside the printable ASCII range 0x20-0x7E (e.g. BBC Micro
    teletext control codes embedded in PRINT strings) are replaced with a
    two-digit hex escape.  A literal backslash followed by 'x' is escaped
    as \\x5Cx to avoid ambiguity on the reverse trip.

    This is the forward half of a lossless round-trip.  Use unescapeNonAscii()
    to reverse.
    """
    out: list[str] = []

    for ch in line:
        code = ord(ch)
        if code == 0x5C:
            # Escape a literal backslash only when it would be ambiguous
            # (i.e. followed by 'x' and two hex digits).  For simplicity
            # we always escape backslash so the reverse is unambiguous.
            out.append("\\x5C")
        elif 0x20 <= code <= 0x7E:
            out.append(ch)
        else:
            out.append(f"\\x{code:02X}")

    return "".join(out)


def unescapeNonAscii(line: str) -> str:
    """Reverse escapeNonAscii - convert \\xHH sequences back to characters."""
    return _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), line)


def _writeBasicText(
    path: str,
    lines: list[str],
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


def _readBasicText(data: bytes) -> list[str]:
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
    if any(_ESCAPE_RE.search(line) for line in lines):
        lines = [unescapeNonAscii(line) for line in lines]

    return lines


# -----------------------------------------------------------------------
# Sorting (format-agnostic, moved from dfs.py, retyped to DiscEntry)
# -----------------------------------------------------------------------

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

            lines = detokenize(data)
            if pretty:
                lines = prettyPrint(lines)

            for line in lines:
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
                    _writeBasicText(out_path, text_lines, text_mode)
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
                inf_line = formatInf(
                    entry.directory, entry.name,
                    entry.load_addr, entry.exec_addr,
                    entry.length, entry.locked,
                )
                with open(out_path + ".inf", "w", encoding="utf-8") as f:
                    f.write(inf_line + "\n")

    return results


def buildImage(
    source_dir: str,
    output_path: str,
    tracks: int = 80,
    title: str = "",
    boot_option: BootOption = BootOption.OFF,
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

    Files without a .inf sidecar are skipped with a warning printed to
    stderr. The .inf file provides metadata (disc path, load address,
    exec address, lock flag) needed to add the file to the catalogue.

    Args:
        source_dir:  Path to the root directory of extracted files.
        output_path: Path whose extension determines the disc format.
        tracks:      Number of tracks (40 or 80).
        title:       Disc title (up to 12 characters).
        boot_option: Boot option (0-3).

    Returns:
        The assembled disc image as bytes, ready to write to a file.

    Raises:
        DiscError: If a file cannot be added (name conflict, disc full, etc.).
        DFSFormatError: If the output_path extension is unrecognised.
    """
    image = createImage(
        output_path, tracks=tracks, title=title, boot_option=boot_option,
    )

    if len(image) > 1:
        # Double-sided: expect side0/ and side1/ subdirectories.
        for i, side in enumerate(image):
            side_path = os.path.join(source_dir, f"side{i}")
            _walkSourceTree(side, side_path)
    else:
        # Single-sided: the source_dir itself holds the directory tree.
        _walkSourceTree(image[0], source_dir)

    return image.serialize()


def _walkSourceTree(side: DiscSide, fs_dir: str, disc_parent: str = "") -> None:
    """Recursively walk a filesystem directory and add files to a disc side.

    Handles both DFS and ADFS layouts:

    DFS directories (e.g. '$', 'T', '+') are implicit - the directory
    letter is stored as metadata in each file's catalogue entry, so
    addFile is sufficient. There is no separate directory-creation step.

    ADFS directories are explicit container entries on disc that must be
    created with mkdir before files can be placed inside them. Only
    sub-directories below the root ('$') need creating, since the root
    already exists on a freshly formatted image.

    File metadata (disc path, load/exec addresses, lock flag) is read
    from .inf sidecars accompanying each data file.

    Args:
        side:        A DiscSide to add files and directories to.
        fs_dir:      Filesystem directory to walk.
        disc_parent: Accumulated disc path of the current directory.
                     Empty string at the top level.
    """
    import sys

    if not os.path.isdir(fs_dir):
        return

    for name in sorted(os.listdir(fs_dir)):
        # Skip .inf sidecar files - they are read alongside their data file.
        if name.endswith(".inf"):
            continue

        path = os.path.join(fs_dir, name)

        if os.path.isdir(path):
            # Build the disc path for this directory.
            child_path = f"{disc_parent}.{name}" if disc_parent else name

            # For ADFS images, create subdirectories below the root on disc.
            # The root-level directories (like '$') already exist.
            if disc_parent and hasattr(side, "mkdir"):
                side.mkdir(child_path)

            _walkSourceTree(side, path, child_path)
            continue

        if not os.path.isfile(path):
            continue

        # Read the .inf sidecar for disc metadata.
        inf_path = path + ".inf"
        if not os.path.isfile(inf_path):
            print(
                f"Warning: no .inf sidecar for {path}, skipping",
                file=sys.stderr,
            )
            continue

        with open(inf_path, "r", encoding="utf-8") as f:
            inf_line = f.readline().strip()

        inf = parseInf(inf_line)

        # Read the data file.
        with open(path, "rb") as f:
            data = f.read()

        # Retokenize .bas files back to BBC BASIC binary format.
        # The extract step detokenizes BASIC programs into plain text,
        # which is larger than the tokenized form. Re-tokenizing here
        # restores the compact binary representation so the rebuilt
        # disc image does not overflow.
        if name.endswith(".bas") and isBasicExecAddr(inf.exec_addr):
            lines = _readBasicText(data)
            data = tokenize(lines)

        # Add to disc using the path from the .inf sidecar.
        side.addFile(DiscFile(
            path=inf.fullName,
            data=data,
            load_addr=inf.load_addr,
            exec_addr=inf.exec_addr,
            locked=inf.locked,
        ))
