# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""High-level disc operations.

Orchestration layer that composes the lower-level modules - dfs (disc I/O),
detokenize, and pretty - into coherent disc-wide operations.

Layer responsibilities:
    dfs        -- disc format parsing and sector-level I/O
    detokenize -- tokenized binary to plain-text transform
    pretty     -- text post-processing
    disc       -- cross-layer orchestration (this module)
    cli        -- argument parsing, output formatting, and user interaction

All operations that span more than one lower layer belong here.
"""

import os
import re
from typing import Dict, List, Optional, Union

from .dfs import (
    BootOption,
    DFSError,
    createDiscImage,
    looksLikeTokenizedBasic,
    looksLikePlainText,
    openDiscImage,
)
from .detokenize import detokenize
from .inf import formatInf, parseInf
from .pretty import prettyPrint


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

    image = openDiscImage(image_path)

    for disc in image.sides:
        catalogue = disc.readCatalogue()

        for entry in catalogue.entries:
            # Scope to a specific file when requested.
            if filename is not None:
                if entry.fullName != filename and entry.name != filename:
                    continue

            if not entry.isBasic:
                continue

            data = disc.readFile(entry)
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
                        "side": disc.side,
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

    image = openDiscImage(image_path)
    multi_side = len(image.sides) > 1

    results = []

    for disc in image.sides:
        catalogue = disc.readCatalogue()

        for entry in catalogue.entries:
            safe_dir = sanitizeDfsDir(entry.directory)
            safe_name = sanitizeDfsFilename(entry.name)
            stem = resolveOutputPath(out_dir, disc.side, safe_dir, safe_name, multi_side)
            data = disc.readFile(entry)

            if entry.isBasic and looksLikeTokenizedBasic(data):
                # Detokenize BASIC and write as plain text.
                out_path = stem + ".bas"
                text_lines = detokenize(data)
                if pretty:
                    text_lines = prettyPrint(text_lines)
                with open(out_path, "w", encoding="ascii", errors="replace") as f:
                    f.write("\n".join(text_lines) + "\n")
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
                with open(out_path + ".inf", "w", encoding="ascii") as f:
                    f.write(inf_line + "\n")

    return results


def buildImage(
    source_dir: str,
    tracks: int = 80,
    is_dsd: bool = False,
    title: str = "",
    boot_option: BootOption = BootOption.OFF,
) -> bytes:
    """Build a disc image from a directory of files with .inf sidecars.

    The source directory is expected to be in the hierarchical layout
    produced by extractAll: one subdirectory per DFS directory character
    (e.g. '$/', 'T/'), with each data file accompanied by a .inf sidecar.

    For double-sided images (is_dsd=True), the source directory should
    contain side0/ and side1/ subdirectories.

    Files without a .inf sidecar are skipped with a warning printed to
    stderr. The .inf file provides the DFS directory, load address, exec
    address, and lock flag needed to add the file to the catalogue.

    Args:
        source_dir:  Path to the root directory of extracted files.
        tracks:      Number of tracks (40 or 80).
        is_dsd:      True for double-sided interleaved format.
        title:       Disc title (up to 12 characters).
        boot_option: Boot option (0-3).

    Returns:
        The assembled disc image as bytes, ready to write to a file.

    Raises:
        DFSError: If a file cannot be added (name conflict, disc full, etc.).
    """
    import sys

    image = createDiscImage(
        tracks=tracks,
        is_dsd=is_dsd,
        title=title,
        boot_option=boot_option,
    )

    if is_dsd:
        # Expect side0/ and side1/ subdirectories.
        side_dirs = []
        for side_index in range(2):
            side_path = os.path.join(source_dir, f"side{side_index}")
            if os.path.isdir(side_path):
                side_dirs.append((side_index, side_path))
    else:
        # Single-sided: the source_dir itself holds the DFS directories.
        side_dirs = [(0, source_dir)]

    for side_index, side_path in side_dirs:
        side = image.sides[side_index]
        _addFilesFromDir(side, side_path)

    return image.serialize()


def _addFilesFromDir(side: "DFSSide", side_path: str) -> None:
    """Add all files with .inf sidecars from a directory tree to a disc side.

    Walks the DFS directory subdirectories (e.g. '$/', 'T/') under
    side_path, reads each .inf sidecar to get DFS metadata, then adds
    the corresponding data file to the disc side.

    Args:
        side:      DFSSide to add files to.
        side_path: Path to the directory containing DFS dir subdirectories.
    """
    import sys

    # Walk each DFS directory subdirectory.
    if not os.path.isdir(side_path):
        return

    for dir_entry in sorted(os.listdir(side_path)):
        dir_path = os.path.join(side_path, dir_entry)

        if not os.path.isdir(dir_path):
            continue

        for file_entry in sorted(os.listdir(dir_path)):
            # Skip .inf files themselves.
            if file_entry.endswith(".inf"):
                continue

            data_path = os.path.join(dir_path, file_entry)

            if not os.path.isfile(data_path):
                continue

            inf_path = data_path + ".inf"

            if not os.path.isfile(inf_path):
                print(
                    f"Warning: no .inf sidecar for {data_path}, skipping",
                    file=sys.stderr,
                )
                continue

            # Read the .inf sidecar.
            with open(inf_path, "r", encoding="ascii") as f:
                inf_line = f.readline().strip()

            inf = parseInf(inf_line)

            # Read the data file.
            with open(data_path, "rb") as f:
                data = f.read()

            side.addFile(
                name=inf.name,
                directory=inf.directory,
                data=data,
                load_addr=inf.load_addr,
                exec_addr=inf.exec_addr,
                locked=inf.locked,
            )
