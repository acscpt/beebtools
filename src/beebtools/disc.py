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

from .dfs import isBasic, looksLikeText, looksLikePlainText, openDiscImage
from .detokenize import detokenize
from .pretty import prettyPrint


# Characters that are illegal in Windows filenames, used when building
# safe output paths from DFS names.
_WINDOWS_ILLEGAL = set('\\/:*?"<>|')


def sanitizeDfsName(dfs_dir: str, dfs_name: str) -> str:
    """Build a safe output filename stem from a DFS directory and name.

    The DFS separator '.' is replaced with '_'. Any character that is
    illegal in a Windows filename is replaced with _xNN_ (its ASCII hex
    value) to guarantee uniqueness - two distinct illegal source characters
    will never produce the same output. Control characters are dropped.

    Examples:
        '$', 'BOOT'   -> '$_BOOT'
        'T', 'MYPROG' -> 'T_MYPROG'
        'T', 'A/B'    -> 'T_A_x2F_B'  (slash encoded)
        'T', 'A\\B'   -> 'T_A_x5C_B'  (backslash encoded, distinct)

    Args:
        dfs_dir:  Single-character DFS directory prefix (e.g. 'T', '$').
        dfs_name: DFS filename, up to 7 characters (e.g. 'MYPROG').

    Returns:
        Safe filename stem with no extension (e.g. 'T_MYPROG').
    """
    parts = []
    for ch in f"{dfs_dir}_{dfs_name}":
        if ord(ch) < 0x20:
            # Drop control characters.
            continue
        if ch in _WINDOWS_ILLEGAL:
            # Encode as _xNN_ so each illegal char maps to a unique string.
            parts.append(f"_x{ord(ch):02X}_")
        else:
            parts.append(ch)
    return "".join(parts)


def resolveOutputPath(
    out_dir: str,
    disc_side: int,
    base: str,
    multi_side: bool,
    sides_mode: Optional[str],
) -> str:
    """Resolve the output path for one file during bulk extraction.

    When the disc has only one side, sides_mode is ignored and the file is
    written directly into out_dir.

    When the disc has two sides:
    - sides_mode 'subdir' or None (default): write into out_dir/side0/ or out_dir/side1/
    - sides_mode 'prefix'                  : write into out_dir with side0_ or side1_ prefix

    Args:
        out_dir:    Root output directory.
        disc_side:  Side number (0 or 1) for this file.
        base:       Sanitized filename stem (e.g. T_MYPROG).
        multi_side: True when the image has more than one side.
        sides_mode: 'subdir', 'prefix', or None.

    Returns:
        Path string to write the file to (extension not included).
    """
    if not multi_side:
        return os.path.join(out_dir, base)

    if sides_mode == "prefix":
        return os.path.join(out_dir, f"side{disc_side}_{base}")

    # Default for double-sided: subdir mode.
    side_dir = os.path.join(out_dir, f"side{disc_side}")
    os.makedirs(side_dir, exist_ok=True)
    return os.path.join(side_dir, base)


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

    for disc in openDiscImage(image_path):
        _title, entries = disc.readCatalogue()

        for entry in entries:
            full_name = f"{entry['dir']}.{entry['name']}"

            # Scope to a specific file when requested.
            if filename is not None:
                if full_name != filename and entry["name"] != filename:
                    continue

            if not isBasic(entry):
                continue

            data = disc.readFile(entry)
            if not looksLikeText(data):
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
                        "filename": full_name,
                        "line_number": int(line[:5]),
                        "line": line,
                    })

    return results


def extractAll(
    image_path: str,
    out_dir: str,
    sides_mode: Optional[str] = None,
    pretty: bool = False,
) -> List[Dict[str, Union[str, int]]]:
    """Extract every file from a disc image into a directory.

    BASIC programs are saved as .bas plain text files.
    Plain text files are saved as .txt with CR normalised to LF.
    Binary files are saved as .bin raw bytes.

    On double-sided images, files are separated by side. The sides_mode
    parameter controls the layout:
    - None or 'subdir' (default): files go into side0/ and side1/ subdirectories
    - 'prefix'                  : files are prefixed with side0_ or side1_ in a flat layout

    Args:
        image_path: Path to a .ssd or .dsd disc image.
        out_dir:    Directory to write extracted files into. Created if absent.
        sides_mode: Layout for double-sided discs: 'subdir', 'prefix', or None.
        pretty:     Apply pretty-printer spacing to BASIC output when True.

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

    sides = openDiscImage(image_path)
    multi_side = len(sides) > 1

    results = []

    for disc in sides:
        _title, entries = disc.readCatalogue()

        for entry in entries:
            base = sanitizeDfsName(entry['dir'], entry['name'])
            stem = resolveOutputPath(out_dir, disc.side, base, multi_side, sides_mode)
            data = disc.readFile(entry)

            if isBasic(entry) and looksLikeText(data):
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
                    "load": entry["load"],
                    "exec": entry["exec"],
                    "length": entry["length"],
                })

    return results
