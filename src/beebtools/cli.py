# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""CLI commands for beebtools.

Implements the 'cat' and 'extract' subcommands and the main() entry point.
"""

import argparse
import os
import sys
from argparse import Namespace
from typing import Optional

from .detokenize import detokenize
from .pretty import prettyPrint
from .dfs import isBasic, looksLikeText, looksLikePlainText, openDiscImage, sortCatalogueEntries


def cmdCat(args: Namespace) -> None:
    """Print the disc catalogue to stdout.

    Args:
        args: Parsed argparse namespace for the 'cat' subcommand.
    """
    sides = openDiscImage(args.image)

    for disc in sides:
        title, entries = disc.readCatalogue()
        side_label = f"Side {disc.side}"
        header = f"--- {side_label}"

        if title:
            header += f": {title}"

        header += f" ({len(entries)} files) ---"
        print(header)
        print()

        if not entries:
            print("  (empty)")
        else:
            orderedEntries = sortCatalogueEntries(entries, args.sort)
            print(f"  {'Name':<12s} {'Load':>8s} {'Exec':>8s} {'Length':>8s}  {'Type'}")

            for e in orderedEntries:
                if isBasic(e):
                    ftype = "BASIC"
                elif args.inspect and looksLikePlainText(disc.readFile(e)):
                    ftype = "TEXT"
                else:
                    ftype = ""
                lock = "L" if e["locked"] else " "
                full_name = f"{e['dir']}.{e['name']}"
                print(
                    f"  {lock}{full_name:<11s} "
                    f"{e['load']:08X} "
                    f"{e['exec']:08X} "
                    f"{e['length']:08X}  "
                    f"{ftype}"
                )
        print()


# Characters that are illegal in Windows filenames.
_WINDOWS_ILLEGAL = set('\\/:*?"<>|')


def _sanitizeDfsName(dfs_dir: str, dfs_name: str) -> str:
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


def _resolveOutputPath(
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


def _extractAll(args: Namespace) -> None:
    """Extract every file from a disc image into a directory.

    BASIC programs are saved as .bas plain text files.
    Binary files are saved as .bin raw bytes.

    On double-sided images, files are always separated by side. The --sides
    flag controls the layout:
    - subdir (default): files go into side0/ and side1/ subdirectories
    - prefix          : files are prefixed with side0_ or side1_ in a flat layout

    Args:
        args: Parsed argparse namespace for the 'extract' subcommand.
    """
    sides = openDiscImage(args.image)

    # Default output directory is the image filename stem (disc39 for disc39.dsd).
    if args.dir:
        out_dir = args.dir
    else:
        out_dir = os.path.splitext(os.path.basename(args.image))[0]

    os.makedirs(out_dir, exist_ok=True)

    multi_side = len(sides) > 1
    sides_mode = getattr(args, "sides", None)

    for disc in sides:
        _title, entries = disc.readCatalogue()

        for entry in entries:
            base = _sanitizeDfsName(entry['dir'], entry['name'])
            stem = _resolveOutputPath(out_dir, disc.side, base, multi_side, sides_mode)
            data = disc.readFile(entry)

            if isBasic(entry) and looksLikeText(data):
                # Detokenize BASIC and write as plain text.
                out_path = stem + ".bas"
                text_lines = detokenize(data)
                if args.pretty:
                    text_lines = prettyPrint(text_lines)
                with open(out_path, "w", encoding="ascii", errors="replace") as f:
                    f.write("\n".join(text_lines) + "\n")
                print(f"  BASIC   {out_path}")

            elif looksLikePlainText(data):
                # Plain ASCII text file - save as .txt.
                # BBC text editors use CR (0x0D) only as a line terminator.
                # Normalise to Unix LF so the output file is portable.
                out_path = stem + ".txt"
                text = data.decode("ascii", errors="replace")
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                with open(out_path, "w", encoding="ascii", errors="replace") as f:
                    f.write(text)
                print(f"  text    {out_path}")

            else:
                # Write binary file and report addressing metadata.
                out_path = stem + ".bin"
                with open(out_path, "wb") as f:
                    f.write(data)
                print(
                    f"  binary  {out_path}  "
                    f"load=0x{entry['load']:06X}  "
                    f"exec=0x{entry['exec']:06X}  "
                    f"length={entry['length']} bytes"
                )


def cmdExtract(args: Namespace) -> None:
    """Extract a file (or all files) from the disc image.

    Args:
        args: Parsed argparse namespace for the 'extract' subcommand.
    """
    # --all routes to the bulk extractor.
    if args.all:
        if args.output:
            print("Error: -o/--output cannot be used with -a/--all. Use -d/--dir instead.",
                  file=sys.stderr)
            sys.exit(1)
        _extractAll(args)
        return

    if not args.filename:
        print("Error: filename required unless -a/--all is specified.", file=sys.stderr)
        sys.exit(1)

    sides = openDiscImage(args.image)
    target = args.filename
    found = None

    if len(target) >= 3 and target[1] == ".":
        # Explicit directory prefix given (e.g. T.MYPROG).
        target_dir = target[0].upper()
        target_name = target[2:]

        for disc in sides:
            _title, entries = disc.readCatalogue()
            for e in entries:
                if (e["dir"].upper() == target_dir
                        and e["name"].upper() == target_name.upper()):
                    found = (disc, e)
                    break
            if found:
                break

    else:
        # Bare filename - find a unique match across all sides and directories.
        target_name = target
        matches = []

        for disc in sides:
            _title, entries = disc.readCatalogue()
            for e in entries:
                if e["name"].upper() == target_name.upper():
                    matches.append((disc, e))

        if len(matches) == 1:
            found = matches[0]
        elif len(matches) > 1:
            print(
                f"Ambiguous filename '{target_name}' - specify with directory prefix.",
                file=sys.stderr,
            )
            for disc, entry in matches:
                print(f"  Side {disc.side}: {entry['dir']}.{entry['name']}",
                      file=sys.stderr)
            sys.exit(1)

    if not found:
        print(f"File not found: {target}", file=sys.stderr)
        sys.exit(1)

    disc, entry = found
    data = disc.readFile(entry)
    full_name = f"{entry['dir']}.{entry['name']}"

    if isBasic(entry) and looksLikeText(data):
        # Detokenize and emit as LIST-style text.
        text_lines = detokenize(data)
        if args.pretty:
            text_lines = prettyPrint(text_lines)
        output = "\n".join(text_lines) + "\n"

        if args.output:
            with open(args.output, "w", encoding="ascii", errors="replace") as f:
                f.write(output)
            print(f"Extracted to {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(output)

    else:
        # Binary file.
        if args.output:
            with open(args.output, "wb") as f:
                f.write(data)
            print(f"Extracted to {args.output}")
            print(
                f"{full_name}  "
                f"load=0x{entry['load']:06X}  "
                f"exec=0x{entry['exec']:06X}  "
                f"length={entry['length']} bytes"
            )
        else:
            # Raw bytes to stdout for piping to a disassembler.
            sys.stdout.buffer.write(data)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "BBC Micro DFS disc image tool. "
            "Read catalogues, extract files, and detokenize BBC BASIC programs "
            "from .ssd and .dsd disc images."
        ),
        epilog="Use 'beebtools <command> -h' for detailed help on each command.",
    )
    sub = parser.add_subparsers(dest="command")

    p_cat = sub.add_parser("cat", help="List disc catalogue")
    p_cat.add_argument("image", help="Path to .ssd or .dsd disc image")
    p_cat.add_argument(
        "-s", "--sort",
        choices=["name", "catalog", "size"],
        default="name",
        help="Sort order: name (default), catalog, or size",
    )
    p_cat.add_argument(
        "-i", "--inspect",
        action="store_true",
        help="Read file contents to detect TEXT files (slower; default is metadata-only)",
    )

    p_extract = sub.add_parser("extract", help="Extract a file, or all files with -a")
    p_extract.add_argument("image", help="Path to .ssd or .dsd disc image")
    p_extract.add_argument("filename", nargs="?",
                           help="DFS filename, e.g. T.MYPROG or MYPROG")
    p_extract.add_argument("-a", "--all", action="store_true",
                           help="Extract all files from the disc")
    p_extract.add_argument("-o", "--output",
                           help="Write single file to this path instead of stdout")
    p_extract.add_argument("-d", "--dir",
                           help="Output directory for -a/--all (default: image name)")
    p_extract.add_argument("--pretty", action="store_true",
                           help="Add operator spacing to BASIC output")
    p_extract.add_argument(
        "-s", "--sides",
        choices=["subdir", "prefix"],
        default=None,
        help=(
            "How to separate files from each side of a double-sided disc "
            "when using -a/--all. "
            "'subdir' (default for double-sided discs) writes into side0/ and side1/ "
            "subdirectories; "
            "'prefix' prepends side0_ or side1_ to each filename in a flat layout."
        ),
    )

    args = parser.parse_args()

    if args.command == "cat":
        cmdCat(args)
    elif args.command == "extract":
        cmdExtract(args)
    else:
        parser.print_help()
